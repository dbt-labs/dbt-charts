"""A cols row's titled items share a first baseline and a body top."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence

import pytest
import vl_convert

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import TitleShift
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render import layout_sizing
from dbt_charts.core.render.chart.table import title_ascent
from dbt_charts.core.render.title_band import TitleMetrics

from .._svg_normalize import normalize_same_run_svg
from ._svg_render import render_board_to_svg

_NS = "{http://www.w3.org/2000/svg}"
_TRANSLATE = re.compile(r"translate\(\s*([-\d.e]+)[ ,]+([-\d.e]+)\s*\)")


def _board(rows: str, style: str = "") -> str:
    return f"""
title: T
{style}
queries:
  series:
    type: values
    rows:
      - {{month: Jan, revenue: 100, region: East}}
      - {{month: Feb, revenue: 300, region: East}}
      - {{month: Jan, revenue: 50, region: West}}
      - {{month: Feb, revenue: 80, region: West}}
  parts:
    type: values
    rows:
      - {{region: North, revenue: 40}}
      - {{region: South, revenue: 30}}
      - {{region: East, revenue: 12}}
      - {{region: West, revenue: 9}}
      - {{region: Central, revenue: 6}}
      - {{region: Islands, revenue: 0.4}}
  q:
    type: values
    rows:
      - {{month: Jan, revenue: 1130000}}
      - {{month: Feb, revenue: 1500000}}
      - {{month: Mar, revenue: 1200000}}
      - {{month: Apr, revenue: 1800000}}
charts:
  wide: {{title: Wide title, query: q, type: bar, x: month, y: revenue}}
  narrow: {{title: Narrow title, query: q, type: bar, x: month, y: revenue}}
  bare: {{query: q, type: bar, x: month, y: revenue}}
  subtitled: {{title: Subtitled title, subtitle: A subtitle, query: q, type: bar, x: month, y: revenue}}
  tab: {{title: Table title, query: q, type: table}}
  spark: {{title: Spark title, query: q, type: spark_bar, x: revenue, y: month}}
  ends:
    title: Endpoint title
    query: series
    type: line
    x: month
    y: revenue
    color: region
    style:
      endpoint_labels:
        visible: true
  donut: {{title: Donut title, query: parts, type: pie, theta: revenue, color: region}}
  stat: {{label: Big number, query: q, type: kpi, value: revenue}}
rows:
{rows}
"""


_STYLE = resolve_style(get_theme_style())
_CARD_PADDING = float(_STYLE.frame.card_padding)


_MIN_TITLE_SIZE = 11.0


def _font_size(element: ET.Element) -> float:
    match = re.match(r"[\d.]+", element.get("font-size") or "")
    return float(match[0]) if match else 0.0


def _baselines_and_plot_tops(svg: str) -> tuple[dict[str, float], dict[str, float]]:
    """Absolute first-baseline y of each title, and body-top y of each chart.

    Baselines are keyed by title text. Body tops are keyed by position in the
    document: a Vega chart's plot top, and for a chart inside a titled board the
    card padding below the chart's own box, which is where its ink begins.
    """
    baselines: dict[str, float] = {}
    plot_tops: list[float] = []

    def walk(element: ET.Element, y: float, in_bare: bool = False) -> None:
        match = _TRANSLATE.search(element.get("transform") or "")
        if match:
            y += float(match[2])
        if element.tag == _NS + "svg":
            y += float(element.get("y") or 0)
        if element.tag == _NS + "g" and element.get("data-chart-id") == "bare":
            plot_tops.append(y + _CARD_PADDING)
            in_bare = True
        elif (
            element.tag == _NS + "g"
            and element.get("stroke-miterlimit")
            and match
            and not in_bare
        ):
            plot_tops.append(y)
        if element.tag == _NS + "text":
            text = "".join(element.itertext()).strip()
            own_y = element.get("y")
            if text and _font_size(element) >= _MIN_TITLE_SIZE:
                baselines.setdefault(text, y + (float(own_y) if own_y else 0.0))
        for child in element:
            walk(child, y, in_bare)

    walk(ET.fromstring(svg), 0.0)
    return baselines, {str(i): v for i, v in enumerate(plot_tops)}


def _all_baselines(svg: str, text: str) -> list[float]:
    """Absolute baseline y of every text element reading ``text``, in document order."""
    found: list[float] = []

    def walk(element: ET.Element, y: float) -> None:
        match = _TRANSLATE.search(element.get("transform") or "")
        if match:
            y += float(match[2])
        if element.tag == _NS + "svg":
            y += float(element.get("y") or 0)
        if element.tag == _NS + "text" and "".join(element.itertext()).strip() == text:
            own_y = element.get("y")
            found.append(y + (float(own_y) if own_y else 0.0))
        for child in element:
            walk(child, y)

    walk(ET.fromstring(svg), 0.0)
    return found


def _plot_top_list(svg: str) -> list[float]:
    return list(_baselines_and_plot_tops(svg)[1].values())


def _plot_overhang() -> float:
    """How far a bar chart's plot sits below the body top Vega-Lite reserves.

    Read off a row of equal titles, which shifts nothing: its measured
    baseline-to-plot gap less the modeled gap (title size plus offset, less the
    ascent). The size is read off the drawn title; the offset is the theme's.
    """
    svg = render_board_to_svg(_board("  - cols:\n      - wide\n      - wide\n"))
    baselines, tops = _baselines_and_plot_tops(svg)
    size = float(re.search(r'font-size="([\d.]+)(?:px)?"[^>]*>Wide Title', svg)[1])
    offset = _STYLE.title.position.offset
    assert offset is not None
    modeled_gap = size + offset - title_ascent(size)
    return tops["0"] - baselines["Wide Title"] - modeled_gap


def test_chart_beside_nested_board_share_baseline_and_body_top() -> None:
    svg = render_board_to_svg(
        _board(
            """
  - cols:
      - wide
      - title: Board title
        rows:
          - bare
"""
        )
    )
    baselines, tops = _baselines_and_plot_tops(svg)
    assert abs(baselines["Wide Title"] - baselines["Board Title"]) <= 0.5 + 1e-6
    assert abs(tops["0"] - _plot_overhang() - tops["1"]) <= 0.5 + 1e-6


def test_two_to_one_row_shares_baseline_and_body_top() -> None:
    svg = render_board_to_svg(
        _board(
            """
  - cols:
      - width: 66%
        rows:
          - wide
      - narrow
"""
        )
    )
    baselines, tops = _baselines_and_plot_tops(svg)
    assert abs(baselines["Wide Title"] - baselines["Narrow Title"]) <= 0.5 + 1e-6
    assert abs(tops["0"] - tops["1"]) <= 0.5 + 1e-6


def test_wrapped_board_title_drops_neighbor_body_not_its_baseline() -> None:
    svg = render_board_to_svg(
        _board(
            """
  - cols:
      - title: A board title long enough that it has to wrap onto a second line here
        rows:
          - bare
      - narrow
      - narrow
"""
        )
    )
    baselines, tops = _baselines_and_plot_tops(svg)
    wrapped = next(v for k, v in baselines.items() if k.startswith("A Board Title"))
    assert abs(baselines["Narrow Title"] - wrapped) <= 0.5 + 1e-6
    assert abs(tops["0"] - tops["1"] + _plot_overhang()) <= 0.5 + 1e-6
    assert abs(tops["1"] - tops["2"]) <= 0.5 + 1e-6


def test_untitled_item_keeps_its_top_edge() -> None:
    alone = render_board_to_svg(_board("  - cols:\n      - bare\n      - bare\n"))
    beside_titled = render_board_to_svg(
        _board("  - cols:\n      - bare\n      - wide\n")
    )
    _, alone_tops = _baselines_and_plot_tops(alone)
    _, beside_tops = _baselines_and_plot_tops(beside_titled)
    assert beside_tops["0"] == alone_tops["0"]


def test_row_of_equal_titles_renders_as_if_no_band_were_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = "  - cols:\n      - narrow\n      - narrow\n"
    banded = normalize_same_run_svg(render_board_to_svg(_board(rows)))
    monkeypatch.setattr(
        layout_sizing,
        "row_title_shifts",
        lambda metrics: [TitleShift() for _ in metrics],
    )
    assert normalize_same_run_svg(render_board_to_svg(_board(rows))) == banded


def test_subtitle_is_part_of_the_band_so_plots_stay_level() -> None:
    svg = render_board_to_svg(_board("  - cols:\n      - subtitled\n      - narrow\n"))
    baselines, tops = _baselines_and_plot_tops(svg)
    assert abs(baselines["Subtitled Title"] - baselines["Narrow Title"]) <= 0.5 + 1e-6
    assert abs(tops["0"] - tops["1"]) <= 0.5 + 1e-6


def test_table_beside_chart_shares_baseline_and_keeps_every_row() -> None:
    svg = render_board_to_svg(
        _board(
            """
  - cols:
      - width: 66%
        rows:
          - wide
      - tab
"""
        )
    )
    baselines, _ = _baselines_and_plot_tops(svg)
    assert abs(baselines["Wide Title"] - baselines["Table Title"]) <= 0.5 + 1e-6
    assert "1.13M" in baselines and any(k.startswith("1.8") for k in baselines)


def test_kpi_has_no_title_slot_and_joins_no_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = "  - cols:\n      - stat\n      - wide\n"
    banded = normalize_same_run_svg(render_board_to_svg(_board(rows)))
    monkeypatch.setattr(
        layout_sizing,
        "row_title_shifts",
        lambda metrics: [TitleShift() for _ in metrics],
    )
    assert normalize_same_run_svg(render_board_to_svg(_board(rows))) == banded


def test_authored_subtitle_size_is_part_of_the_band() -> None:
    svg = render_board_to_svg(
        _board(
            "  - cols:\n      - subtitled\n      - narrow\n",
            style="style:\n  title:\n    subtitle:\n      font:\n        size: 14",
        )
    )
    baselines, tops = _baselines_and_plot_tops(svg)
    assert abs(baselines["Subtitled Title"] - baselines["Narrow Title"]) <= 0.5 + 1e-6
    assert abs(tops["0"] - tops["1"]) <= 0.5 + 1e-6


def test_spec_leaves_subtitle_spacing_to_vega_defaults() -> None:
    """The band models Vega's default subtitle padding and line height, so the
    spec must not set either."""
    specs: list[dict[str, object]] = []
    original = vl_convert.vegalite_to_svg

    def spy(spec: object, *args: object, **kwargs: object) -> str:
        specs.append(json.loads(spec) if isinstance(spec, str) else spec)
        return original(spec, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(vl_convert, "vegalite_to_svg", spy)
        render_board_to_svg(_board("  - subtitled\n"))
    text = json.dumps(specs[-1])
    assert "subtitlePadding" not in text
    assert "subtitleLineHeight" not in text


def test_spark_bar_beside_chart_shares_baseline() -> None:
    svg = render_board_to_svg(
        _board(
            """
  - cols:
      - width: 66%
        rows:
          - wide
      - spark
"""
        )
    )
    baselines, _ = _baselines_and_plot_tops(svg)
    assert abs(baselines["Wide Title"] - baselines["Spark Title"]) <= 0.5 + 1e-6


def test_null_title_offset_still_renders_a_banded_row() -> None:
    svg = render_board_to_svg(
        _board(
            "  - cols:\n      - width: 66%\n        rows:\n          - wide\n      - narrow\n",
            style="style:\n  title:\n    position:\n      offset: null",
        )
    )
    baselines, _ = _baselines_and_plot_tops(svg)
    assert abs(baselines["Wide Title"] - baselines["Narrow Title"]) <= 0.5 + 1e-6


def test_stacked_charts_in_a_wrapper_still_get_the_band() -> None:
    """A wrapper holding two charts is never re-rendered by height alignment, so
    the shifted render has to be the one the final pass finds."""
    svg = render_board_to_svg(
        _board(
            """
  - cols:
      - width: 66%
        rows:
          - wide
      - rows:
          - narrow
          - bare
"""
        )
    )
    baselines, _ = _baselines_and_plot_tops(svg)
    assert abs(baselines["Wide Title"] - baselines["Narrow Title"]) <= 0.5 + 1e-6


def test_one_chart_in_two_rows_gets_each_rows_own_band() -> None:
    rows = """
  - cols:
      - width: 66%
        rows:
          - wide
      - narrow
  - cols:
      - width: 66%
        rows:
          - bare
      - narrow
"""
    alone = "  - cols:\n      - width: 66%\n        rows:\n          - bare\n      - narrow\n"
    svg = render_board_to_svg(_board(rows))
    second_row_only = render_board_to_svg(_board(alone))

    def narrow_gap(markup: str, last: bool) -> float:
        positions = _all_baselines(markup, "Narrow Title")
        tops = _plot_top_list(markup)
        return tops[-1 if last else 0] - positions[-1 if last else 0]

    assert narrow_gap(svg, last=True) == narrow_gap(second_row_only, last=True)


def test_board_cell_with_a_shift_moves_its_layout_with_its_title() -> None:
    """An inner board whose title is smaller than its neighbor's is the cell that
    gets the shift; its charts have to move with its title."""
    nested = """
  - title: Outer board title
    cols:
      - title: Inner board title
        rows:
          - bare
      - wide
"""
    svg = render_board_to_svg(_board(nested))
    baselines, tops = _baselines_and_plot_tops(svg)
    assert abs(baselines["Inner Board Title"] - baselines["Wide Title"]) <= 0.5 + 1e-6
    assert abs(tops["1"] - _plot_overhang() - tops["0"]) <= 0.5 + 1e-6


def test_endpoint_label_rail_stays_on_its_series_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = "  - cols:\n      - ends\n      - title: Board title\n        rows:\n          - bare\n"

    def label_to_endpoint(markup: str) -> list[float]:
        """Each label's distance from its line's last point, East then West."""
        top = _plot_top_list(markup)[0]
        ends = [
            float(m[1])
            for m in re.finditer(
                r'class="mark-line role-mark[^"]*".*?d="M[^"]*L[\d.]+,([\d.]+)"', markup
            )
        ][:2]
        labels = [_all_baselines(markup, n)[0] for n in ("East", "West")]
        return [label - (top + end) for label, end in zip(labels, ends, strict=True)]

    shifted = label_to_endpoint(render_board_to_svg(_board(rows)))
    monkeypatch.setattr(
        layout_sizing, "row_title_shifts", lambda m: [TitleShift() for _ in m]
    )
    plain = label_to_endpoint(render_board_to_svg(_board(rows)))
    assert shifted == pytest.approx(plain, abs=0.5)


def test_hidden_cell_does_not_join_the_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = """
  - cols:
      - wide
      - narrow
      - visible: false
        title: A hidden board title that would set the tallest tail in the row
        rows:
          - bare
"""
    seen: list[int] = []
    original = layout_sizing.row_title_shifts

    def spy(metrics: Sequence[TitleMetrics | None]) -> list[TitleShift]:
        seen.append(len(metrics))
        return original(metrics)

    monkeypatch.setattr(layout_sizing, "row_title_shifts", spy)
    render_board_to_svg(_board(rows))
    assert seen == [2]


def test_title_shift_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="bodydy"):
        TitleShift.model_validate({"title_dy": 3.0, "bodydy": 5.0})


def _chart_block(svg: str, chart_id: str) -> str:
    """The wrapper group a chart is drawn in, through its closing tag."""
    start = svg.index(f'id="chart-{chart_id}"')
    start = svg.rfind("<g", 0, start)
    depth = 0
    for tag in re.finditer(r"<g\b|</g>", svg[start:]):
        depth += 1 if tag[0] == "<g" else -1
        if depth == 0:
            return normalize_same_run_svg(svg[start : start + tag.end()])
    raise AssertionError(f"chart {chart_id} block never closes")


def _unbanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        layout_sizing, "row_title_shifts", lambda m: [TitleShift() for _ in m]
    )


def test_hidden_leading_item_in_a_wrapper_does_not_set_the_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = """
  - cols:
      - wide
      - rows:
          - visible: false
            title: A hidden board title that would set the tallest tail in the row
            rows:
              - bare
          - narrow
"""
    seen: list[list[TitleShift]] = []
    original = layout_sizing.row_title_shifts

    def spy(metrics: Sequence[TitleMetrics | None]) -> list[TitleShift]:
        seen.append(original(metrics))
        return seen[-1]

    monkeypatch.setattr(layout_sizing, "row_title_shifts", spy)
    render_board_to_svg(_board(rows))
    assert seen == [[TitleShift(), TitleShift()]]


def test_card_gap_does_not_split_a_board_body_from_its_neighbor() -> None:
    svg = render_board_to_svg(
        _board(
            "  - cols:\n      - wide\n      - title: Board title\n        rows:\n          - bare\n",
            style="card_gap: true",
        )
    )
    baselines, tops = _baselines_and_plot_tops(svg)
    assert abs(baselines["Wide Title"] - baselines["Board Title"]) <= 0.5 + 1e-6
    assert abs(tops["0"] - _plot_overhang() - tops["1"]) <= 0.5 + 1e-6


def test_attached_table_pie_stays_out_of_the_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = "  - cols:\n      - donut\n      - title: Board title\n        rows:\n          - bare\n"
    banded = _chart_block(render_board_to_svg(_board(rows)), "donut")
    _unbanded(monkeypatch)
    assert _chart_block(render_board_to_svg(_board(rows)), "donut") == banded


def test_spark_bar_title_and_bars_follow_their_shift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Beside a chart whose title is much larger (29px) the spark's own ascent is the
    smaller one, so its title moves by a large, unmistakable amount; the bars
    below it move with the band and the card grows."""
    big = "style:\n  title:\n    sizes: [24, 29, 14, 14, 11, 11]"
    rows = "  - cols:\n      - width: 66%\n        rows:\n          - wide\n      - spark\n"
    banded = render_board_to_svg(_board(rows, style=big))
    _unbanded(monkeypatch)
    plain = render_board_to_svg(_board(rows, style=big))

    def card(markup: str) -> tuple[float, float, float]:
        block = _chart_block(markup, "spark")
        height = float(re.search(r'data-chart-height="([\d.]+)"', block)[1])
        first_bar = float(re.search(r'<rect[^>]*?y="([\d.]+)"[^>]*rx=', block)[1])
        return height, first_bar, _baselines_and_plot_tops(markup)[0]["Spark Title"]

    shifted = card(banded)
    unshifted = card(plain)
    title_dy = shifted[2] - unshifted[2]
    assert title_dy > 5
    baselines = _baselines_and_plot_tops(banded)[0]
    assert abs(baselines["Wide Title"] - baselines["Spark Title"]) <= 0.5 + 1e-6
    assert shifted[0] - unshifted[0] >= title_dy
    assert shifted[1] - unshifted[1] == pytest.approx(
        shifted[0] - unshifted[0], abs=0.5
    )
