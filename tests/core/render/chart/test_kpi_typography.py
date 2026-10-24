"""KPI slot typography: every font field reaches the SVG and the measurer."""

from __future__ import annotations

import html
import re
from typing import Any

import pytest

from dbt_charts.core.compile.models.chart.resolved import ResolvedKpiChart
from dbt_charts.core.compile.models.query.normalized import AnyQuery, ValuesQuery
from dbt_charts.core.compile.resolve import resolve

from ..._engine_render import render_kpi_svg

_ROWS = [{"revenue": 128_000, "delta": 0.12}]
_TONE_POSITIVE = "positive"


def _render(
    chart_def: dict[str, Any],
    width: float | None = None,
    rows: list[dict[str, Any]] = _ROWS,
) -> str:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.normalize.charts import normalize_chart
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    board_rs, board_ctx = resolve_style_and_context(
        get_theme_style(get_default_theme_name())
    )
    registry: dict[str, AnyQuery] = {"q": ValuesQuery(rows=rows)}
    flat = normalize_chart("chart1", chart_def, registry, sources={})
    resolved = resolve(flat, rows, chart_style_context=board_ctx)
    assert isinstance(resolved, ResolvedKpiChart)
    return render_kpi_svg(resolved, rows, width, board_style=board_rs)


def _text_xs(svg: str) -> list[float]:
    return [float(m) for m in re.findall(r'<text x="([^"]+)"', svg)]


def _kpi(style: dict[str, Any], variant: str = "stacked") -> dict[str, Any]:
    return {
        "type": "kpi",
        "query": "q",
        "value": "revenue",
        "label": "Revenue",
        "glyph": "●",
        "support": {"value": "delta", "label": "vs last month", "glyph": "▲"},
        "style": _merge({"variant": variant, "value": {"format": "$,.0f"}}, style),
    }


def _tspan(svg: str, text: str) -> str:
    """The opening tag of the tspan (or label text) holding ``text``."""
    # A stacked label carries its attributes on <text>, around per-line tspans.
    pattern = (
        r"<(?:tspan|text)\b[^>]*>(?:<title>[^<]*</title>)?"
        rf'\s*(?:<tspan x="[^"]*" y="[^"]*">)?{re.escape(text)}'
    )
    match = re.search(pattern, svg)
    assert match is not None, f"no element holds {text!r} in {svg}"
    return match.group(0)


def _attr(tag: str, name: str) -> str | None:
    match = re.search(rf' {name}="([^"]*)"', tag)
    return None if match is None else match.group(1)


# (style path to the slot's font, text the slot paints)
_SLOTS = {
    "value": (("value", "font"), "128,000"),
    "affix": (("value", "affix", "font"), "$"),
    "glyph": (("value", "glyph", "font"), "● "),
    "label": (("label", "font"), "Revenue"),
    "support": (("support", "font"), "0.12"),
    "explainer": (("support", "font"), " vs last month"),
    "support_glyph": (("support", "glyph", "font"), "▲ "),
}

_FIELDS = {
    "family": (
        "Georgia, serif",
        "font-family",
        "Georgia, 'Noto Emoji', serif",
    ),
    "size": (31, "font-size", "31.0"),
    "weight": (800, "font-weight", "800"),
    "style": ("italic", "font-style", "italic"),
    "decoration": ("underline", "text-decoration", "underline"),
}


def _nest(path: tuple[str, ...], leaf: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = leaf
    for key in reversed(path):
        out = {key: out}
    return out


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for key, value in b.items():
        out[key] = (
            _merge(out[key], value)
            if isinstance(value, dict) and isinstance(out.get(key), dict)
            else value
        )
    return out


@pytest.mark.parametrize("variant", ["stacked", "inline", "compact"])
@pytest.mark.parametrize("field", sorted(_FIELDS))
@pytest.mark.parametrize("slot", sorted(_SLOTS))
def test_every_font_field_reaches_every_slot(
    slot: str, field: str, variant: str
) -> None:
    path, text = _SLOTS[slot]
    value, attr, emitted = _FIELDS[field]
    style = _nest(path, {field: value})
    svg = _render(_kpi(style, variant), width=900)
    assert html.unescape(_attr(_tspan(svg, text), attr) or "") == emitted


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ({"label": {"font": {"case": "upper"}}}, "REVENUE"),
        ({"support": {"font": {"case": "upper"}}}, "VS LAST MONTH"),
        ({"value": {"glyph": {"font": {"case": "upper"}}}}, ">X </tspan>"),
        ({"support": {"glyph": {"font": {"case": "upper"}}}}, ">Y </tspan>"),
    ],
    ids=["label", "support", "glyph", "support_glyph"],
)
def test_case_reaches_the_slot_text(style: dict[str, Any], expected: str) -> None:
    chart = _kpi(style)
    chart["glyph"] = "x"
    chart["support"]["glyph"] = "y"
    assert expected in _render(chart)


def test_case_reaches_a_text_value() -> None:
    svg = _render(
        _kpi({"value": {"font": {"case": "upper"}}}),
        rows=[{"revenue": "at risk", "delta": 0.12}],
    )
    assert ">AT RISK</tspan>" in svg


def test_chart_font_reaches_every_slot_it_does_not_set() -> None:
    svg = _render(_kpi({"font": {"size": 20}}))
    assert _attr(_tspan(svg, "0.12"), "font-size") == "20.0"


def test_conditional_formatting_beats_value_slot_colors() -> None:
    chart = _kpi(
        {
            "value": {
                "font": {"color": "#111111"},
                "affix": {"font": {"color": "#222222"}},
                "glyph": {"font": {"color": "#333333"}},
            }
        }
    )
    chart["conditional_formatting"] = {
        "revenue": {"when": [{"gt": 0, "font": {"color": "#00aa00"}}]}
    }
    svg = _render(chart)
    for text in ("128,000", "$", "● "):
        assert _attr(_tspan(svg, text), "fill") == "#00aa00"


def test_conditional_formatting_beats_support_slot_colors() -> None:
    chart = _kpi(
        {
            "support": {
                "tone": _TONE_POSITIVE,
                "font": {"color": "#111111"},
                "glyph": {"font": {"color": "#222222"}},
            }
        }
    )
    chart["conditional_formatting"] = {
        "delta": {"when": [{"gt": 0, "font": {"color": "#00aa00"}}]}
    }
    svg = _render(chart)
    assert _attr(_tspan(svg, "0.12"), "fill") == "#00aa00"
    assert _attr(_tspan(svg, "▲ "), "fill") == "#00aa00"


def test_affix_case_reaches_the_suffix() -> None:
    svg = _render(
        _kpi(
            {
                "value": {
                    "format": {"spec": ",.0f", "suffix": " units"},
                    "affix": {"font": {"case": "upper"}},
                }
            }
        )
    )
    assert "UNITS" in svg


def test_slot_colors_paint_their_own_slot() -> None:
    svg = _render(
        _kpi(
            {
                "value": {
                    "format": "$,.0f",
                    "font": {"color": "#111111"},
                    "affix": {"font": {"color": "#222222"}},
                    "glyph": {"font": {"color": "#333333"}},
                },
                "label": {"font": {"color": "#444444"}},
            }
        )
    )
    assert _attr(_tspan(svg, "128,000"), "fill") == "#111111"
    assert _attr(_tspan(svg, "$"), "fill") == "#222222"
    assert _attr(_tspan(svg, "● "), "fill") == "#333333"
    assert _attr(_tspan(svg, "Revenue"), "fill") == "#444444"


def test_affix_and_glyph_follow_the_value_fill_when_unset() -> None:
    svg = _render(_kpi({"value": {"format": "$,.0f", "font": {"color": "#111111"}}}))
    assert _attr(_tspan(svg, "$"), "fill") == "#111111"
    assert _attr(_tspan(svg, "● "), "fill") == "#111111"


def _support_fills(style: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    svg = _render(_kpi({"support": style}))
    return (
        _attr(_tspan(svg, "0.12"), "fill"),
        _attr(_tspan(svg, "▲ "), "fill"),
        _attr(_tspan(svg, " vs last month"), "fill"),
    )


def _tone_hex() -> str:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    resolved, _ = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    return resolved.tones.positive


def _secondary() -> str:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    resolved, _ = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    return resolved.secondary_color


def test_support_unstyled_is_secondary() -> None:
    secondary = _secondary()
    assert _support_fills({}) == (secondary, secondary, secondary)


def test_support_tone_paints_value_and_glyph() -> None:
    tone = _tone_hex()
    assert _support_fills({"tone": _TONE_POSITIVE}) == (tone, tone, _secondary())


def test_support_font_color_beats_tone_on_the_value_only() -> None:
    tone = _tone_hex()
    fills = _support_fills({"tone": _TONE_POSITIVE, "font": {"color": "#123456"}})
    assert fills == ("#123456", tone, _secondary())


def test_support_glyph_falls_back_to_the_row_color_without_a_tone() -> None:
    fills = _support_fills({"font": {"color": "#123456"}})
    assert fills == ("#123456", "#123456", _secondary())


def test_support_glyph_color_beats_tone() -> None:
    tone = _tone_hex()
    fills = _support_fills(
        {"tone": _TONE_POSITIVE, "glyph": {"font": {"color": "#654321"}}}
    )
    assert fills == (tone, "#654321", _secondary())


def test_compact_right_column_follows_the_value_weight() -> None:
    light = _render(_kpi({"value": {"font": {"weight": 300}}}, "compact"))
    heavy = _render(_kpi({"value": {"font": {"weight": 800}}}, "compact"))
    assert _text_xs(heavy)[1] > _text_xs(light)[1]


def test_right_aligned_label_is_measured_at_its_weight() -> None:
    light = _render(_kpi({"align": "right", "label": {"font": {"weight": 300}}}))
    heavy = _render(_kpi({"align": "right", "label": {"font": {"weight": 800}}}))
    assert _text_xs(heavy)[1] < _text_xs(light)[1]


def test_right_aligned_label_is_measured_italic() -> None:
    upright = _render(
        _kpi({"align": "right", "label": {"font": {"family": "Source Serif 4"}}})
    )
    italic = _render(
        _kpi(
            {
                "align": "right",
                "label": {"font": {"family": "Source Serif 4", "style": "italic"}},
            }
        )
    )
    assert _text_xs(italic)[1] != _text_xs(upright)[1]


def test_right_aligned_label_is_measured_after_case() -> None:
    plain = _render(_kpi({"align": "right", "label": {"font": {"case": "none"}}}))
    upper = _render(_kpi({"align": "right", "label": {"font": {"case": "upper"}}}))
    assert _text_xs(upper)[1] < _text_xs(plain)[1]


def test_label_overflow_comes_from_the_label_slot() -> None:
    long_label = "A very long KPI label that cannot possibly fit a narrow card"
    chart = _kpi({"label": {"overflow": "truncate"}})
    chart["label"] = long_label
    svg = _render(chart, width=120)
    assert "…" in svg
    assert svg.count("<tspan x=") == 1
