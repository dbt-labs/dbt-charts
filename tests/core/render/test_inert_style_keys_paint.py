"""Style keys that validate must change the render or be rejected."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

from ..._svg_normalize import normalize_same_run_svg
from .._svg_render import pinned_clock

_BOARD = """\
queries:
  q:
    type: values
    rows:
      - {a: x, b: 1}
      - {a: y, b: 2}
charts:
  bar:
    type: bar
    query: q
    x: a
    y: b
  tbl:
    type: table
    query: q
rows:
  - bar
  - tbl
  - text: "Block text"
"""
_RED = "#ff0000"
_CARD_BORDER = re.compile(r'<rect class="dbt-card-border"[^>]*>')
_CARD_SHADOW = re.compile(r'<rect class="dbt-card-shadow"[^>]*>')


@pytest.fixture
def svg_of(
    local_project: Callable[..., FilesystemProject],
) -> Callable[[str], str]:
    def _svg(extra: str = "", board: str = _BOARD) -> str:
        result = compile_board(board + extra)
        assert result.success and result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
            clock=pinned_clock,
        )
        out = render(result.board, executor, format="svg")
        assert out.board_error is None, out.board_error
        assert isinstance(out.output, str)
        return normalize_same_run_svg(out.output)

    return _svg


def _style(yaml_body: str) -> str:
    return "style:\n" + "".join(f"  {line}\n" for line in yaml_body.splitlines())


def test_no_card_chrome_unless_authored(svg_of: Callable[[str], str]) -> None:
    svg = svg_of()
    assert not _CARD_BORDER.findall(svg)
    assert not _CARD_SHADOW.findall(svg)


def test_charts_border_paints_on_each_chart_card(
    svg_of: Callable[[str], str],
) -> None:
    svg = svg_of(
        _style(f"charts:\n  border:\n    width: 4\n    color: '{_RED}'\n    radius: 6")
    )
    borders = _CARD_BORDER.findall(svg)
    assert len(borders) == 2
    for rect in borders:
        assert f'stroke="{_RED}"' in rect
        assert 'stroke-width="4.0"' in rect
        assert 'rx="4.0"' in rect


def test_table_chart_style_border_paints(svg_of: Callable[[str], str]) -> None:
    board = _BOARD.replace(
        "  tbl:\n    type: table\n    query: q\n",
        f"  tbl:\n    type: table\n    query: q\n    style:\n      border:\n"
        f"        width: 4\n        color: '{_RED}'\n",
    )
    borders = _CARD_BORDER.findall(svg_of(board=board))
    assert len(borders) == 1
    assert f'stroke="{_RED}"' in borders[0]


def test_table_border_overrides_board_default_field_by_field(
    svg_of: Callable[[str], str],
) -> None:
    svg = svg_of(
        _style(
            "charts:\n  border:\n    width: 2\n    color: '#00ff00'\n"
            f"  table:\n    border:\n      color: '{_RED}'"
        )
    )
    bar, table = _CARD_BORDER.findall(svg)
    assert 'stroke="#00ff00"' in bar and 'stroke-width="2.0"' in bar
    assert f'stroke="{_RED}"' in table and 'stroke-width="2.0"' in table


def test_point_map_border_overrides_board_default(
    svg_of: Callable[..., str],
) -> None:
    board = _BOARD.replace(
        "rows:\n  - bar\n",
        "  pts:\n    type: point_map\n    query: geo\n    latitude: lat\n"
        "    longitude: lon\nrows:\n  - pts\n  - bar\n",
    ).replace(
        "charts:\n",
        "  geo:\n    type: values\n    rows:\n      - {lat: 40.7, lon: -74.0}\n"
        "      - {lat: 34.0, lon: -118.2}\ncharts:\n",
        1,
    )
    svg = svg_of(
        _style(
            "charts:\n  border:\n    width: 2\n    color: '#00ff00'\n"
            f"  point_map:\n    border:\n      color: '{_RED}'"
        ),
        board=board,
    )
    map_card, bar, table = _CARD_BORDER.findall(svg)
    assert f'stroke="{_RED}"' in map_card
    assert 'stroke="#00ff00"' in bar and 'stroke="#00ff00"' in table


def _with_chart(chart_yaml: str, query_yaml: str, row: str) -> str:
    head, rows = _BOARD.split("rows:\n  - bar\n")
    queries, charts = head.split("charts:\n")
    return (
        f"{queries}{query_yaml}charts:\n{charts}{chart_yaml}rows:\n  - {row}\n"
        f"  - bar\n{rows}"
    )


def test_kpi_card_inherits_charts_border_and_honors_its_own(
    svg_of: Callable[..., str],
) -> None:
    board = _with_chart(
        "  k:\n    type: kpi\n    query: one\n    value: b\n",
        "  one:\n    type: values\n    rows:\n      - {b: 5}\n",
        "k",
    )
    inherited = svg_of(
        _style(f"charts:\n  border:\n    width: 4\n    color: '{_RED}'"), board=board
    )
    kpi_card = _CARD_BORDER.findall(inherited)[0]
    assert f'stroke="{_RED}"' in kpi_card and 'stroke-width="4.0"' in kpi_card
    own = svg_of(
        _style(
            f"charts:\n  border:\n    width: 4\n    color: '{_RED}'\n"
            "  kpi:\n    border:\n      width: 1\n      color: '#00ff00'\n"
            "      radius: 0"
        ),
        board=board,
    )
    assert 'stroke="#00ff00"' in _CARD_BORDER.findall(own)[0]


def test_kpi_card_border_dash_array_reaches_the_stroke(
    svg_of: Callable[..., str],
) -> None:
    board = _with_chart(
        "  k:\n    type: kpi\n    query: one\n    value: b\n",
        "  one:\n    type: values\n    rows:\n      - {b: 5}\n",
        "k",
    )
    svg = svg_of(
        _style(
            "charts:\n  kpi:\n    border:\n      width: 1\n      color: '#333333'\n"
            "      radius: 4\n      dash_array: [4, 4]\n      line_cap: round"
        ),
        board=board,
    )
    kpi_card = _CARD_BORDER.findall(svg)[0]
    assert 'stroke-dasharray="4,4"' in kpi_card
    assert 'stroke-linecap="round"' in kpi_card


def test_callout_keeps_its_own_border_and_shadow_radius(
    svg_of: Callable[..., str],
) -> None:
    board = _with_chart(
        "  note:\n    type: callout\n    title: Note\n    message: Body\n", "", "note"
    )
    svg = svg_of(
        _style(
            f"box_shadow: '0 2px 8px #000000'\ncharts:\n  border:\n    width: 4\n    color: '{_RED}'"
        ),
        board=board,
    )
    assert len(_CARD_BORDER.findall(svg)) == 2
    callout_shadow = _CARD_SHADOW.findall(svg)[0]
    assert 'rx="4.0"' in callout_shadow


def test_rounded_card_clips_its_content(svg_of: Callable[..., str]) -> None:
    svg = svg_of(_style("charts:\n  border:\n    width: 2\n    radius: 8"))
    clips = re.findall(r'<clipPath id="(dbt-card-clip-[^"]+)"><rect[^>]*rx="8.0"', svg)
    assert len(clips) == 2
    assert all(f'clip-path="url(#{clip})"' in svg for clip in clips)
    assert "dbt-card-clip" not in svg_of()


def test_geoshape_border_overrides_board_default(svg_of: Callable[..., str]) -> None:
    board = _with_chart(
        "  geo:\n    type: geoshape\n    query: states\n    geo_source: us-states\n"
        "    lookup: state_id\n    color: population\n",
        "  states:\n    type: values\n    rows:\n      - {state_id: 6, population: 1}\n"
        "      - {state_id: 48, population: 2}\n",
        "geo",
    )
    svg = svg_of(
        _style(
            "charts:\n  border:\n    width: 2\n    color: '#00ff00'\n"
            f"  geoshape:\n    border:\n      color: '{_RED}'"
        ),
        board=board,
    )
    geo_card = _CARD_BORDER.findall(svg)[0]
    assert f'stroke="{_RED}"' in geo_card
    assert 'stroke-width="2.0"' in geo_card


def test_shadow_fill_is_the_charts_own_canvas(svg_of: Callable[..., str]) -> None:
    board = _BOARD.replace(
        "  bar:\n    type: bar\n",
        "  bar:\n    type: bar\n    style:\n      background: '#112233'\n",
    )
    svg = svg_of(_style("box_shadow: '0 2px 8px #000000'"), board=board)
    bar_shadow = _CARD_SHADOW.findall(svg)[0]
    assert 'fill="#112233"' in bar_shadow


def test_shadow_follows_card_corner_radius(svg_of: Callable[..., str]) -> None:
    svg = svg_of(
        _style("box_shadow: '0 2px 8px #000000'\ncharts:\n  border:\n    radius: 6")
    )
    shadows = _CARD_SHADOW.findall(svg)
    assert shadows and all('rx="6.0"' in rect for rect in shadows)


def test_box_shadow_paints_on_chart_cards(svg_of: Callable[[str], str]) -> None:
    svg = svg_of(_style("box_shadow: '0 2px 8px #000000'"))
    shadows = _CARD_SHADOW.findall(svg)
    assert len(shadows) == 2
    for rect in shadows:
        assert "drop-shadow(0px 2px 8px #000000)" in rect


@pytest.mark.parametrize(
    "shadow",
    ["inset 0 2px 8px #000", "0 2px 8px 3px #000", "0 2px", "blur everything"],
)
def test_unsupported_box_shadow_is_rejected(shadow: str) -> None:
    result = compile_board(_BOARD + _style(f"box_shadow: {shadow!r}"))
    assert not result.success


_BLOCK_FONT = "family: \"'Source Code Pro', monospace\"\nsize: 28"


def _text_block(svg_of: Callable[..., str], style: str) -> str:
    board = _BOARD.replace(
        '  - text: "Block text"\n',
        '  - text: "Block text"\n    style:\n' + style,
    )
    return svg_of(board=board)


def test_block_font_family_and_size_apply_to_text(
    svg_of: Callable[..., str],
) -> None:
    plain = svg_of()
    block = _text_block(
        svg_of,
        "      font:\n        " + "\n        ".join(_BLOCK_FONT.splitlines()) + "\n",
    )
    via_text = _text_block(
        svg_of,
        "      text:\n        font:\n          "
        + "\n          ".join(_BLOCK_FONT.splitlines())
        + "\n",
    )
    assert block != plain
    assert "Source Code Pro" in block
    assert block == via_text


def test_block_text_font_wins_over_block_font(svg_of: Callable[..., str]) -> None:
    both = _text_block(
        svg_of,
        "      font:\n        size: 28\n      text:\n        font:\n          size: 20\n",
    )
    text_only = _text_block(svg_of, "      text:\n        font:\n          size: 20\n")
    assert both == text_only
