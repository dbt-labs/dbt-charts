"""A table too wide or too long for its slot scrolls inside it.

`column_layout.overflow: scroll` (the default) keeps compact columns at their
width and scrolls sideways; `fit` squeezes them into the slot. `freeze` pins
leading columns while the table scrolls, and `pagination.mode: scroll` swaps
the pager for a vertical scroll region under a pinned header.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.render_result import RenderResult

from ...._compile import plan_of
from ..._svg_render import render_board_svg_result

_WIDE_COLUMNS = 16


def _board(style: str = "", rows: int = 3, columns: int = _WIDE_COLUMNS) -> str:
    keys = [f"account_metric_{i}" for i in range(columns)]
    body = "\n".join(
        "      - {"
        + ", ".join(f"{k}: {1000 * r + i}" for i, k in enumerate(keys))
        + "}"
        for r in range(rows)
    )
    chart_style = f"    style:\n{style}" if style else ""
    return (
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t:\n    query: q\n    type: table\n"
        + chart_style
        + "rows:\n  - t\n"
    )


def _render(yaml_text: str, *, controls: bool = False) -> tuple[str, RenderResult]:
    result = render_board_svg_result(yaml_text, controls=controls)
    assert isinstance(result.output, str)
    return result.output, result


def _codes(result: RenderResult) -> set[str]:
    return {w.code for w in result.warnings}


# The warnings a squeezed table raises.
_SQUEEZE = {
    "WARN-TABLE-COLUMNS-OVERFLOW",
    "WARN-TABLE-HEADER-EXTRA-WRAP",
    "WARN-TABLE-TEXT-TRUNCATED",
}


def _max(svg: str, axis: str) -> float:
    match = re.search(rf'data-max-{axis}="([^"]+)"', svg)
    assert match, "expected a scrolling table"
    return float(match.group(1))


def test_a_table_wider_than_its_slot_scrolls_instead_of_squeezing() -> None:
    svg, result = _render(_board())
    assert _max(svg, "x") > 0
    assert _max(svg, "y") == 0
    assert not _SQUEEZE & _codes(result)
    assert 'data-dbt-scroll-thumb="x"' in svg


def _rightmost_text_x(svg: str) -> float:
    return max(float(x) for x in re.findall(r'<text x="([-\d.]+)"', svg))


def test_fit_squeezes_the_same_table() -> None:
    scroll, _ = _render(_board())
    fit, _ = _render(_board("      column_layout: {overflow: fit}\n"))
    assert "data-dbt-table-scroll" not in fit
    # Squeezed, the last column sits further left than at natural width.
    assert _rightmost_text_x(fit) < _rightmost_text_x(scroll)


def test_a_table_that_fits_draws_the_same_under_either_mode() -> None:
    scroll, _ = _render(_board(columns=3))
    fit, _ = _render(_board("      column_layout: {overflow: fit}\n", columns=3))
    assert "data-dbt-table-scroll" not in scroll
    assert scroll == fit


def test_a_long_text_column_wraps_before_the_table_scrolls() -> None:
    text = "a long free text note " * 12
    yaml_text = (
        "queries:\n  q:\n    type: values\n    rows:\n"
        f"      - {{id: 1, amount: 12.5, note: '{text}'}}\n"
        "charts:\n  t: {query: q, type: table}\nrows:\n  - t\n"
    )
    svg, _ = _render(yaml_text)
    assert "data-dbt-table-scroll" not in svg
    assert re.search(r"<text[^>]*data-col=\"2\"[^>]*><tspan", svg), "note wraps"


_SVG_NS = "{http://www.w3.org/2000/svg}"


def _pane(svg: str, pane: str) -> ET.Element:
    panes = [
        g
        for g in ET.fromstring(svg).iter(f"{_SVG_NS}g")
        if f"table-scroll-t-{pane}-" in g.get("clip-path", "")
    ]
    assert len(panes) == 1, f"expected one {pane} pane"
    return panes[0]


def _pane_columns(svg: str, pane: str) -> set[str]:
    """The `data-col` values drawn anywhere inside the named scroll pane."""
    return {el.get("data-col") for el in _pane(svg, pane).iter() if el.get("data-col")}


def test_freeze_pins_the_leading_columns_in_their_own_panes() -> None:
    svg, _ = _render(_board("      column_layout: {freeze: 1}\n"))
    assert _pane_columns(svg, "frozen") == {"0"}
    assert "0" in _pane_columns(svg, "rows")
    assert 'data-dbt-scroll-shadow="x"' in svg


def test_the_frozen_edge_is_a_hairline_a_fade_and_unbroken_rules() -> None:
    svg, _ = _render(_board("      column_layout: {freeze: 1}\n"))
    root = ET.fromstring(svg)
    (shade,) = [
        g for g in root.iter(f"{_SVG_NS}g") if g.get("data-dbt-scroll-shadow") == "x"
    ]
    opacities = [float(r.get("fill-opacity")) for r in shade.iter(f"{_SVG_NS}rect")]
    assert opacities == sorted(opacities, reverse=True)
    assert opacities[0] == 0.12
    # The rules painted over the edge are the rules alone, cells left out.
    head = _pane(svg, "edge-head")
    body = _pane(svg, "edge-body")
    assert list(head.iter(f"{_SVG_NS}rect")), "the header rule crosses the edge"
    assert not list(head.iter(f"{_SVG_NS}text"))
    assert not list(body.iter(f"{_SVG_NS}text"))
    # The fade and the rules run the full table, header included.
    shade_rect = next(shade.iter(f"{_SVG_NS}rect"))
    hairline = [
        r
        for r in root.iter(f"{_SVG_NS}rect")
        if r.get("width") == "1"
        and r.get("height") == shade_rect.get("height")
        and r not in list(shade.iter(f"{_SVG_NS}rect"))
    ]
    assert len(hairline) == 1


def test_each_header_label_is_drawn_once_with_columns_frozen() -> None:
    svg, _ = _render(_board("      column_layout: {freeze: 1}\n"), controls=True)
    assert _pane_columns(svg, "corner") == {"0"}
    assert "0" not in _pane_columns(svg, "header")
    assert svg.count("data-dbt-sortable=") == _WIDE_COLUMNS


def test_a_live_board_ships_no_script_and_a_static_export_does() -> None:
    live, _ = _render(_board(), controls=True)
    assert "data-dbt-table-scroll" in live
    assert "<script" not in live
    assert "tabindex" not in live
    static, _ = _render(_board())
    assert "window.dbtTableScroll" in static


def test_frozen_columns_wider_than_half_the_table_are_an_error() -> None:
    _, result = _render(_board("      column_layout: {freeze: 12}\n"))
    assert "ERR-TABLE-FREEZE-INVALID" in str(result.chart_errors)
    assert "freeze fewer columns" in str(result.chart_errors)


def test_a_default_table_too_long_to_scroll_pages_and_warns_once() -> None:
    svg, result = _render(_board(rows=1001, columns=2))
    capped = [w for w in result.warnings if w.code == "WARN-TABLE-SCROLL-ROWS-CAPPED"]
    assert len(capped) == 1
    assert "data-max-y" not in svg
    assert "dbt-paginator" in svg


def test_a_chart_pagination_keeps_the_board_scroll_mode() -> None:
    yaml_text = _board(
        "      pagination: {enabled: true, page_rows: 10}\n", rows=40, columns=3
    ).replace(
        "rows:\n  - t\n",
        "style:\n  charts:\n    table:\n      pagination: {mode: scroll}\nrows:\n  - t\n",
    )
    svg, _ = _render(yaml_text)
    assert _max(svg, "y") > 0


def test_scroll_mode_shows_page_rows_under_a_pinned_header() -> None:
    svg, _ = _render(
        _board("      pagination: {mode: scroll, page_rows: 10}\n", rows=40, columns=3)
    )
    assert _max(svg, "y") > 0
    assert 'data-dbt-scroll-range="40"' in svg
    pager = svg[svg.index('data-dbt-scroll-pager="1"') :]
    label = re.search(r'class="dbt-paginator-label"[^>]*>([^<]*)</text>', pager)
    assert label, "the paged table's pager sits under the scrolling rows"
    assert re.fullmatch(r"1\u20131?\d / 40", label.group(1))
    assert 'data-paginator-role="next"' in pager
    assert len(re.findall(r'<text[^>]*data-col="0"', svg)) == 40
    assert svg.index('font-weight="600"') < svg.index('data-col="0"'), "header first"


def _range_attr(svg: str, name: str) -> str:
    match = re.search(rf'data-dbt-scroll-range[^>]*{name}="([^"]+)"', svg)
    assert match, f"expected a scroll range with {name}"
    return match.group(1)


def test_the_viewport_is_page_rows_and_a_half_row_and_the_last_row_lands_flush() -> (
    None
):
    svg, _ = _render(
        _board("      pagination: {mode: scroll, page_rows: 10}\n", rows=40, columns=3)
    )
    ends = [float(v) for v in _range_attr(svg, "data-row-ends").split(",")]
    row_h = ends[0]
    assert float(_range_attr(svg, "data-viewport")) == pytest.approx(10.5 * row_h)
    assert _range_attr(svg, "data-page-rows") == "10"
    assert _max(svg, "y") == pytest.approx(30 * row_h)


def _total_board(rows: int) -> str:
    body = "\n".join(
        f"      - {{name: r{i}, v: {i}, kind: value}}" for i in range(rows)
    )
    body += "\n      - {name: Total, v: 999, kind: total}"
    return (
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        "charts:\n  t:\n    query: q\n    type: table\n    row_role: kind\n"
        "    style:\n      pagination: {mode: scroll, page_rows: 10}\n"
        "rows:\n  - t\n"
    )


def test_the_total_is_pinned_under_the_viewport_never_scrolled_or_counted() -> None:
    svg, _ = _render(_total_board(40))
    assert 'data-dbt-scroll-range="40"' in svg
    scrolled = svg[svg.index('data-dbt-rows="true"') : svg.index('data-dbt-pinned="t"')]
    assert 'data-dbt-row-role="total"' not in scrolled
    pinned = svg[svg.index('data-dbt-pinned="t"') :]
    assert 'data-dbt-row-role="total"' in pinned
    assert pinned.index('data-dbt-row-role="total"') < pinned.index("dbt-paginator")


def test_a_pinned_total_is_not_counted_as_a_truncated_row() -> None:
    svg, _ = _render(_total_board(40))
    assert 'font-style="italic"' not in svg


def test_freeze_without_scroll_is_an_error() -> None:
    _, result = _render(_board("      column_layout: {overflow: fit, freeze: 1}\n"))
    assert "ERR-TABLE-FREEZE-INVALID" in str(result.chart_errors)
    assert "overflow: scroll" in str(result.chart_errors)


def test_pagination_off_compiles_under_the_scroll_default() -> None:
    result = compile_board(_board("      pagination: {enabled: false}\n"))
    assert result.success


def test_freezing_more_columns_than_drawn_is_an_error() -> None:
    _, result = _render(_board("      column_layout: {freeze: 4}\n", columns=3))
    assert "ERR-TABLE-FREEZE-INVALID" in str(result.chart_errors)


@pytest.mark.parametrize("fmt", ["png", "pdf"])
def test_an_image_or_pdf_export_fits_the_table_instead_of_scrolling(
    fmt: str,
    monkeypatch: pytest.MonkeyPatch,
    local_project: Callable[..., FilesystemProject],
) -> None:
    from dbt_charts.core.execute.lookup import Ledger
    from dbt_charts.core.execute.plan import make_context, utc_now
    from dbt_charts.core.render import renderer as renderer_module

    converted: list[str] = []

    def capture(svg: str, *_args: object) -> bytes:
        converted.append(svg)
        return b"bytes"

    monkeypatch.setattr(renderer_module, f"to_{fmt}", capture)
    # A frozen column is part of scrolling; the export drops it with the scroll.
    result = compile_board(_board("      column_layout: {freeze: 1}\n"))
    assert result.board is not None
    env = build_exec_env(local_project(Path.cwd()))
    ctx = make_context(
        result.board, plan_of(result.board, env, utc_now(), all_queries=True), env
    )
    rendered = renderer_module.render(result.board, ctx, Ledger(), format=fmt)
    assert not rendered.chart_errors
    (svg,) = converted
    assert "data-dbt-table-scroll" not in svg
    # The same board on screen still scrolls.
    screen = renderer_module.render(result.board, ctx, Ledger(), format="svg").output
    assert "data-dbt-table-scroll" in screen
