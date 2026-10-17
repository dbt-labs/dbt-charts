"""`link: {url, open: modal}` stamps `data-dbt-link-open="modal"` on the link's own
anchors; a page-mode link renders exactly as a string link always has."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_ANCHOR_RE = re.compile(r"<a\b[^>]*>")


def _render_svg(board_yaml: str) -> str:
    result = compile_board(board_yaml)
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    rendered = render(result.board, executor, format="svg")
    assert not rendered.chart_errors, rendered.chart_errors
    assert isinstance(rendered.output, str)
    return rendered.output


def _anchors(svg: str, needle: str) -> list[str]:
    return [a for a in _ANCHOR_RE.findall(svg) if needle in a]


_BAR = """
title: Test
queries:
  q:
    type: values
    rows:
      - {region: North, revenue: 10}
      - {region: South, revenue: 20}
charts:
  c:
    type: bar
    query: q
    x: region
    y: revenue
    link: {link}
rows:
  - c
"""


@pytest.mark.parametrize("mode", ["modal", "preview", "new_tab"])
def test_chart_link_mode_stamps_every_mark_anchor(mode: str) -> None:
    svg = _render_svg(
        _BAR.replace(
            "{link}", f"\n      url: /detail?region={{{{ x }}}}\n      open: {mode}"
        )
    )

    anchors = _anchors(svg, "/detail?region=")
    assert len(anchors) == 2
    assert all(f'data-dbt-link-open="{mode}"' in a for a in anchors)


@pytest.mark.parametrize(
    "link",
    ["/detail?region={{ x }}", "\n      url: /detail?region={{ x }}\n      open: page"],
)
def test_page_link_emits_no_open_mode(link: str) -> None:
    svg = _render_svg(_BAR.replace("{link}", link))

    assert _anchors(svg, "/detail?region=")
    assert "data-dbt-link-open" not in svg


def test_absolute_chart_link_renders_plain_href() -> None:
    svg = _render_svg(
        _BAR.replace(
            "{link}",
            "\n      url: https://example.com/{{ x }}\n      open: modal",
        )
    )

    assert "xlink:href" not in svg
    anchors = _anchors(svg, 'href="https://example.com/')
    assert len(anchors) == 2
    assert all('data-dbt-link-open="modal"' in a for a in anchors)


_TABLE = """
title: Test
queries:
  q:
    type: values
    rows:
      - {region: North, revenue: 10}
charts:
  t:
    type: table
    query: q
    link:
      url: /detail?region={{ region }}
      open: modal
    style:
      columns:
        region:
          link: https://example.com/{{ region }}
          header_link:
            url: /regions
            open: modal
rows:
  - t
"""


def test_table_link_modes_are_per_anchor() -> None:
    """A modal row link does not leak onto a page-mode cell link inside it."""
    svg = _render_svg(_TABLE)

    (row,) = _anchors(svg, "/detail?region=North")
    (cell,) = _anchors(svg, "https://example.com/North")
    (header,) = _anchors(svg, '"/regions"')
    assert 'data-dbt-link-open="modal"' in row
    assert "data-dbt-link-open" not in cell
    assert 'data-dbt-link-open="modal"' in header


def test_kpi_modal_link_is_stamped() -> None:
    svg = _render_svg("""
title: Test
queries:
  q:
    type: values
    rows:
      - {revenue: 10}
charts:
  k:
    type: kpi
    query: q
    value: revenue
    link:
      url: /detail
      open: modal
rows:
  - k
""")

    (anchor,) = _anchors(svg, '"/detail"')
    assert 'data-dbt-link-open="modal"' in anchor


@pytest.mark.parametrize(
    ("link", "message"),
    [
        ("\n      url: /x\n      open: popup", "open"),
        ("\n      url: /x\n      target: modal", "target"),
    ],
)
def test_invalid_link_object_is_a_compile_error(link: str, message: str) -> None:
    result = compile_board(_BAR.replace("{link}", link))

    assert not result.success
    assert message in str(result.errors)


def test_modal_column_link_templated_from_a_row_value_compiles() -> None:
    """A column link may be a bare row template; the object form keeps that."""
    result = compile_board("""
title: Test
queries:
  q:
    type: values
    rows:
      - {region: North, u: "/detail?r=North"}
charts:
  t:
    type: table
    query: q
    style:
      columns:
        region:
          link:
            url: "{{ u }}"
            open: modal
rows:
  - t
""")

    assert result.success, result.errors


def test_new_tab_link_opens_without_a_script() -> None:
    """An exported file runs no script: the anchor itself targets a new tab."""
    svg = _render_svg(
        _BAR.replace(
            "{link}", "\n      url: /detail?region={{ x }}\n      open: new_tab"
        )
    )

    anchors = _anchors(svg, "/detail?region=")
    assert len(anchors) == 2
    assert all('target="_blank"' in a for a in anchors)
