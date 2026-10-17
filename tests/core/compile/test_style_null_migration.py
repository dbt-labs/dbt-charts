"""0.9.0 -> 0.10.0: style `null` is a value only on fields where None means none.

Elsewhere it is rejected, so a board that wrote it is recognized as 0.9.0 and
migrated (dbt_charts.core.compile.migrations.versions.v0_10_0).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations.versions.v0_10_0 import _rewrite_style_nulls
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.factories import AUTHORED_INPUT
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

from .._svg_render import pinned_clock, render_board_to_svg

pytestmark = pytest.mark.filterwarnings(
    "ignore::dbt_charts.core.compile.migrations.SchemaMigrationWarning"
)

_BOARD = (
    "queries:\n  q:\n    type: values\n    rows:\n      - {{x: a, y: 1}}\n      - {{x: b, y: 2}}\n"
    "charts:\n  c:\n    query: q\n    type: pie\n    theta: y\n    color: x\n{chart_style}"
    "rows: [c]\n"
)

# (null as written, what the board means now)
NULLS = {
    "pie.inner_radius": (
        "charts:\n  pie:\n    inner_radius: null\n",
        "charts:\n  pie: {{}}\n",
    ),
    "bar.aspect_ratio": (
        "charts:\n  bar:\n    aspect_ratio: null\n",
        "charts:\n  bar: {{}}\n",
    ),
    "legend.title.visible": (
        "charts:\n  legend:\n    title:\n      visible: null\n",
        "charts:\n  legend:\n    title:\n      visible: auto\n",
    ),
}


def _indented(style: str, spaces: int) -> str:
    return "".join(" " * spaces + line + "\n" for line in style.splitlines())


def _svg(source: str, base_dir: Path | None = None) -> str:
    """Rendered output: resolved style shows where a model dump cannot."""
    project = FilesystemProject(base_dir or Path.cwd())
    result = compile(source, base_dir=project.directory() if base_dir else None)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
        clock=pinned_clock,
    )
    output = render(result.board, executor, format="svg").output
    assert output is not None
    return re.sub(r"clip\d+-", "clip-", output)


@pytest.mark.parametrize("field", NULLS)
def test_null_rewrites_to_current_effect_on_the_board(field: str) -> None:
    written, meant = NULLS[field]
    board = _BOARD.format(chart_style="")
    before = f"style:\n{_indented(written, 2)}{board}"
    after = f"style:\n{_indented(meant.replace('{{}}', '{}'), 2)}{board}"
    assert _svg(before) == _svg(after)


@pytest.mark.parametrize("field", NULLS)
def test_null_rewrites_to_current_effect_in_meta(field: str, tmp_path: Path) -> None:
    written, meant = NULLS[field]
    control = tmp_path / "control"
    control.mkdir()
    (tmp_path / "meta.yml").write_text(f"style:\n{_indented(written, 2)}")
    (control / "meta.yml").write_text(
        f"style:\n{_indented(meant.replace('{{}}', '{}'), 2)}"
    )
    board = _BOARD.format(chart_style="")
    assert _svg(board, tmp_path) == _svg(board, control)


def test_null_rewrites_to_current_effect_on_a_chart() -> None:
    before = _BOARD.format(chart_style="    style:\n      inner_radius: null\n")
    after = _BOARD.format(chart_style="    style: {}\n")
    assert _svg(before) == _svg(after)


def test_null_on_absent_field_sets_the_absence() -> None:
    style = {
        "charts": {
            "kpi": {"support": {"tone": None}},
            "axis": {
                "ticks": {"count": None},
                "labels": {"format": None},
                "grid": {"dash": None},
            },
        },
        "footer": {"link": None, "divider": {"dasharray": None}},
    }
    assert _rewrite_style_nulls(style, (StylePatch,)) == style
    parsed = AuthoredBoard.model_validate(
        {"title": "t", "style": style}, context=AUTHORED_INPUT
    )
    assert parsed.style is not None
    assert "link" in parsed.style.footer.model_fields_set


def test_null_on_other_field_fails_naming_the_path() -> None:
    with pytest.raises(ValidationError, match=r"style\.charts\.pie") as raised:
        AuthoredBoard.model_validate(
            {"title": "t", "style": {"charts": {"pie": {"inner_radius": None}}}},
            context=AUTHORED_INPUT,
        )
    assert "inner_radius: null is not accepted" in str(raised.value)


def test_map_entry_null_is_untouched() -> None:
    style = {"formats": {"a": None}, "font": {"size": None}}
    assert _rewrite_style_nulls(style, (StylePatch,)) == {
        "formats": {"a": None},
        "font": {},
    }


_LINE = (
    "queries:\n  q:\n    columns: [x, y]\n    values:\n      - [a, 1]\n      - [b, 2]\n"
    "charts:\n  c:\n    query: q\n    type: line\n    x: x\n    y: y\n"
    "    style:\n{chart_style}"
    "rows: [c]\n{board_style}"
)


def test_valid_chart_null_survives_beside_a_rejected_one() -> None:
    board = _LINE.format(
        board_style=(
            "style:\n  charts:\n    line:\n      marks:\n        line:\n"
            "          stroke:\n            dasharray: 4 2\n"
        ),
        chart_style=(
            "      aspect_ratio: null\n      marks:\n        line:\n"
            "          stroke:\n            dasharray: null\n"
        ),
    )
    dashed = board.replace("aspect_ratio: null", "aspect_ratio: 1").replace(
        "dasharray: null", "dasharray: 4 2"
    )
    assert "stroke-dasharray" in render_board_to_svg(dashed)
    assert "stroke-dasharray" not in render_board_to_svg(board)


def test_rejected_null_under_a_shorter_rule_is_dropped() -> None:
    board = (
        "title: T\nstyle:\n  charts:\n    tooltip:\n      border:\n"
        "        width: null\n"
    )
    assert compile(board).success


_SERIES = """queries:
  q:
    type: values
    rows:
      - {{x: a, y: 1, g: p}}
      - {{x: a, y: 2, g: q}}
      - {{x: b, y: 2, g: p}}
      - {{x: b, y: 3, g: q}}
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
    color: g
rows: [c]
style:
  charts:
    legend:
      title:
        visible: {visible}
"""


def test_legend_title_null_means_auto() -> None:
    def svg(visible: str) -> str:
        return _svg(_SERIES.format(visible=visible))

    assert svg("null") == svg("auto")
    assert svg("auto") != svg("true")


ITEM_POSITIONS = {
    "rows": "rows:\n  - style:\n      charts:\n        pie:\n          inner_radius: null\n    text: x\n",
    "cols": "cols:\n  - style:\n      charts:\n        pie:\n          inner_radius: null\n    text: x\n",
    "grid": "grid:\n  columns: 1\n  items:\n    - item:\n        style:\n          charts:\n            pie:\n              inner_radius: null\n        text: x\n",
    "tabs": "tabs:\n  items:\n    - title: t\n      style:\n        charts:\n          pie:\n            inner_radius: null\n      rows:\n        - text: x\n",
    "two levels deep": "rows:\n  - rows:\n      - style:\n          charts:\n            pie:\n              inner_radius: null\n        text: x\n",
    "three levels deep": "rows:\n  - cols:\n      - rows:\n          - style:\n              charts:\n                pie:\n                  inner_radius: null\n            text: x\n",
    "chart in a nested board": "queries:\n  q:\n    type: values\n    rows:\n      - {x: a, y: 1}\ncharts:\n  c:\n    query: q\n    type: pie\n    theta: y\n    color: x\n    style:\n      inner_radius: null\nrows:\n  - rows:\n      - c\n",
    "inline chart in a nested board": "queries:\n  q:\n    type: values\n    rows:\n      - {x: a, y: 1}\nrows:\n  - rows:\n      - query: q\n        type: pie\n        theta: y\n        color: x\n        style:\n          inner_radius: null\n",
}


@pytest.mark.parametrize("position", ITEM_POSITIONS)
def test_null_in_an_inline_layout_item_style_is_dropped(position: str) -> None:
    result = compile(ITEM_POSITIONS[position])
    assert result.errors == []
    assert result.board is not None


def test_null_on_an_absence_field_survives_an_inline_rows_chart() -> None:
    source = (
        "queries:\n  q:\n    type: values\n    rows:\n      - {x: a, y: 1}\n"
        "rows:\n  - type: bar\n    query: q\n    x: x\n    y: y\n"
        "    style:\n      legend:\n        symbol_limit: null\n"
    )
    result = compile(source)
    assert result.success, result.errors
    (chart,) = result.board.charts.values()
    assert chart.style is not None and chart.style.legend is not None
    assert "symbol_limit" in chart.style.legend.model_fields_set
