"""Engine hooks the thumbnail transform reads, and the end-to-end thumbnail format."""

import re
from pathlib import Path
from xml.etree import ElementTree as ET

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

NS = "{http://www.w3.org/2000/svg}"

BOARD = """
title: Thumbnail Board
queries:
  sales:
    type: values
    rows:
      - {region: North, revenue: 120.5, units: 40}
      - {region: South, revenue: 80, units: 25}
      - {region: East, revenue: 60.25, units: 15}
charts:
  by_region:
    query: sales
    type: bar
    x: region
    y: revenue
  detail:
    query: sales
    type: table
rows:
  - by_region
  - detail
"""


FURNITURE_BOARD = """
title: Furniture
queries:
  sales:
    type: values
    rows:
      - {region: North, kind: A, revenue: 120.5}
      - {region: South, kind: B, revenue: 80}
charts:
  by_region:
    query: sales
    type: bar
    x: region
    y: revenue
    color: kind
rows:
  - by_region
"""

FURNITURE = ("role-axis-label", "role-axis-tick", "role-axis-grid", "role-legend")


def classes(svg: str) -> set[str]:
    return {t for m in re.findall(r'class="([^"]*)"', svg) for t in m.split()}


def render_board(fmt: str, board: str = BOARD, **options: object) -> str:
    result = compile(board)
    assert result.success
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    out = render(result.board, executor, format=fmt, **options).output
    assert isinstance(out, str)
    return out


def test_every_chart_wrapper_names_its_chart_type() -> None:
    root = ET.fromstring(render_board("svg"))
    types = {
        g.get("data-chart-id"): g.get("data-chart-type")
        for g in root.iter(f"{NS}g")
        if "dbt-chart" in (g.get("class") or "").split()
    }
    assert types == {"by_region": "bar", "detail": "table"}


def test_table_cells_carry_column_identity_and_raw_numbers() -> None:
    root = ET.fromstring(render_board("svg"))
    (table,) = [g for g in root.iter(f"{NS}g") if g.get("data-chart-type") == "table"]
    numeric = [t for t in table.iter(f"{NS}text") if t.get("data-value")]
    assert sorted(
        float(t.get("data-value")) for t in numeric if t.get("data-col") == "1"
    ) == [
        60.25,
        80.0,
        120.5,
    ]
    assert {t.get("data-col") for t in numeric} == {"1", "2"}
    text_cells = [
        t
        for t in table.iter(f"{NS}text")
        if t.get("data-col") == "0" and not t.get("data-value")
    ]
    assert len(text_cells) == 3


def test_thumbnail_format_is_the_cartoonized_svg() -> None:
    svg = render_board("svg")
    thumb = render_board("thumbnail")
    root = ET.fromstring(thumb)
    assert not list(root.iter(f"{NS}text"))
    # Measured against the stored (font-free) render. A text-and-table board
    # lands near 0.54; boards dominated by data marks (maps) barely shrink, so
    # no tighter ratio holds across boards.
    assert len(thumb) < 0.6 * len(svg)
    assert root.get("viewBox") == ET.fromstring(svg).get("viewBox")


def test_a_real_board_loses_its_footer_legend_and_axis_furniture() -> None:
    svg = render_board("svg", FURNITURE_BOARD)
    assert {"dbt-footer-link", "role-axis-label", "role-legend"} <= classes(svg)
    left = classes(render_board("thumbnail", FURNITURE_BOARD))
    assert not left & {"dbt-footer-link", *FURNITURE}


def test_a_transparent_board_cartoonizes_to_translucent_ink() -> None:
    board = BOARD + "style:\n  background: transparent\n"
    thumb = render_board("thumbnail", board)
    assert 'fill-opacity="0.2"' in thumb
    assert not list(ET.fromstring(thumb).iter(f"{NS}text"))


def test_board_prose_does_not_survive_the_thumbnail() -> None:
    thumb = render_board("thumbnail")
    assert "Thumbnail Board" not in thumb
