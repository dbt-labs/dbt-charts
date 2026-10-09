"""The chart wrapper names itself to assistive tech without hiding its marks."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from dbt_charts.core.compile.models.board.resolved import ChartIdentity
from dbt_charts.core.render.chart.rendering import _wrap_rendered_chart_svg

_SVG_NS = "{http://www.w3.org/2000/svg}"
_VEGA_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
    '<circle role="graphics-symbol" aria-label="Jan: 5" r="2"/></svg>'
)


def _identity(chart_type: str = "bar", chart_id: str = "rev") -> ChartIdentity:
    return ChartIdentity(
        chart_type=chart_type,
        id=chart_id,
        source_path="",
        defined_in_other_file=False,
        query_name=None,
        notes="",
        variable_dependencies=frozenset(),
        magnitude_colored=False,
        canvas=None,
    )


def _wrap(
    title: str = "Revenue",
    subtitle: str | None = None,
    chart_type: str = "bar",
    is_error_fallback: bool = False,
) -> ET.Element:
    html, _ = _wrap_rendered_chart_svg(
        _identity(chart_type),
        _VEGA_SVG,
        outer_width=100.0,
        inset=None,
        ink_inset={"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0},
        resolved_title=title,
        resolved_subtitle=subtitle,
        is_error_fallback=is_error_fallback,
        card_border=None,
        card_shadow=None,
    )
    return ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{html}</svg>')[0]


def test_wrapper_is_a_named_chart_group_with_no_title_tooltip() -> None:
    wrapper = _wrap()
    assert wrapper.get("role") == "group"
    assert wrapper.get("aria-roledescription") == "chart"
    assert wrapper.get("aria-label") == "Revenue"
    # <title> would paint a native hover tooltip over the whole chart.
    assert wrapper.find(f"{_SVG_NS}title") is None
    assert wrapper.get("aria-labelledby") is None


def test_marks_stay_reachable_inside_the_wrapper() -> None:
    wrapper = _wrap()
    mark = wrapper.find(f".//{_SVG_NS}circle")
    assert mark is not None
    assert mark.get("aria-label") == "Jan: 5"


def test_subtitle_becomes_the_description() -> None:
    wrapper = _wrap(subtitle="Monthly, USD")
    desc = wrapper.find(f"{_SVG_NS}desc")
    assert desc is not None
    assert desc.text == "Monthly, USD"
    assert desc.get("id") is None
    assert wrapper[0] is desc


def test_no_subtitle_means_no_description() -> None:
    wrapper = _wrap(subtitle=None)
    assert wrapper.find(f"{_SVG_NS}desc") is None


def test_title_and_subtitle_are_escaped() -> None:
    wrapper = _wrap(title="A & <B>", subtitle='say "hi" & <go>')
    assert wrapper.get("aria-label") == "A & <B>"
    desc = wrapper.find(f"{_SVG_NS}desc")
    assert desc is not None
    assert desc.text == 'say "hi" & <go>'


def test_callout_and_error_fallback_emit_the_same_valid_wrapper() -> None:
    callout = _wrap(chart_type="callout")
    assert callout.get("role") == "group"
    assert callout.get("aria-label") == "Revenue"
    fallback = _wrap(is_error_fallback=True)
    assert fallback.get("role") == "group"
    assert fallback.find(f"{_SVG_NS}desc") is None
