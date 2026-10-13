"""A native or grain format name never reaches a painter that cannot read it:
each board below renders correct text or fails with a coded diagnostic, never
ERR-INTERNAL."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import InMemoryBoard

_NAMES = ["duration", "bytes"]


def _render(yaml_content: str, tmp_path: Path) -> BoardRenderResult:
    project = FilesystemProject(tmp_path)
    return render_dashboard(
        board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
        project=project,
        adapter_registry=build_adapter_registry(project, read_only=False),
        result_cache=None,
        format="svg",
    )


def _svg(result: BoardRenderResult) -> str:
    assert isinstance(result.data, str)
    return result.data


def _codes(result: BoardRenderResult) -> list[str]:
    diagnostics = [
        *result.validation_errors,
        *result.chart_errors,
        *([result.board_error] if result.board_error is not None else []),
    ]
    return [d.code for d in diagnostics]


def _board(
    chart: str, *, style: str = "", query_formats: str = "", rows: str | None = None
) -> str:
    values = rows or '[["60", 60], ["120", 7200000], ["3600", 950]]'
    return f"""
title: T
{style}
queries:
  q:
    columns: [a, secs]
    values: {values}
{query_formats}
charts:
  c:
    query: q
{chart}
rows:
  - c
"""


_BAR = "    type: bar\n    x: a\n    y: secs\n"


@pytest.mark.parametrize(
    "aliases",
    [
        "    date_month: date_month\n",
        "    date_month:\n      spec: date_month\n",
        "    date_month: date_week\n    date_week: date_week\n",
    ],
)
def test_a_grain_name_that_redefines_nothing_is_a_coded_error(
    aliases: str, tmp_path: Path
) -> None:
    result = _render(_board(_BAR, style=f"style:\n  formats:\n{aliases}"), tmp_path)
    assert _codes(result) == ["ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED"]


def test_a_grain_name_in_the_format_filter_is_a_coded_error(tmp_path: Path) -> None:
    chart = _BAR + "    title: \"S {{ '2024-01-01' | format('date_month') }}\"\n"
    assert _codes(_render(_board(chart), tmp_path)) == [
        "ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED"
    ]


@pytest.mark.parametrize("name", _NAMES)
@pytest.mark.parametrize("chart_type", ["bar", "line"])
def test_a_category_axis_of_number_labels_paints_a_native(
    name: str, chart_type: str, tmp_path: Path
) -> None:
    orientation = "      orientation: vertical\n" if chart_type == "bar" else ""
    chart = (
        f"    type: {chart_type}\n    x: a\n    y: secs\n    style:\n{orientation}"
        f"      axis_x:\n        labels:\n          format: {name}\n"
    )
    assert _codes(_render(_board(chart), tmp_path)) == []


_TABLE = "    type: table\n"
_TABLE_ROWS = '[["a", 3600], ["b", 7200000], ["c", 950]]'


def _text(result: BoardRenderResult) -> str:
    """The text the SVG paints, tags stripped, glyph runs joined."""
    runs = re.findall(r"<text\b[^>]*>(.*?)</text>", _svg(result), flags=re.S)
    return "".join(re.sub(r"<[^>]+>", "", run) for run in runs)


# A table declares a unit once (`symbol_mode: anchors`), so `bytes` keeps its
# unit on the first row only; `duration` spells its units in the number.
_NATIVE_PAINTED = {
    "duration": ["1h", "83d 8h", "15m 50s"],
    "bytes": ["3.6 kB", "7.2", "950"],
}


@pytest.mark.parametrize("name", _NAMES)
@pytest.mark.parametrize("where", ["column", "default", "query"])
def test_a_table_paints_a_native(name: str, where: str, tmp_path: Path) -> None:
    chart, style, query_formats = _TABLE, "", ""
    if where == "column":
        chart += (
            f"    style:\n      columns:\n        secs:\n          format: {name}\n"
        )
    elif where == "default":
        style = f"style:\n  formats:\n    number: {name}"
    else:
        query_formats = f"    formats:\n      secs: {name}"
    result = _render(
        _board(chart, style=style, query_formats=query_formats, rows=_TABLE_ROWS),
        tmp_path,
    )
    assert _codes(result) == []
    painted = _text(result)
    for expected in _NATIVE_PAINTED[name]:
        assert expected in painted


@pytest.mark.parametrize("name", _NAMES)
def test_a_kpi_paints_a_native_from_its_own_format(name: str, tmp_path: Path) -> None:
    kpi = (
        f"    type: kpi\n    value: secs\n    style:\n      value:\n"
        f"        format: {name}\n"
    )
    result = _render(_board(kpi, rows="[[1, 3600]]"), tmp_path)
    assert _codes(result) == []
    assert _NATIVE_PAINTED[name][0] in _text(result)


def test_a_kpi_inherits_a_native_from_the_query(tmp_path: Path) -> None:
    kpi = "    type: kpi\n    value: secs\n"
    result = _render(
        _board(
            kpi, rows="[[1, 3600]]", query_formats="    formats:\n      secs: bytes"
        ),
        tmp_path,
    )
    assert _codes(result) == []
    assert "3.6 kB" in _text(result)


@pytest.mark.parametrize("name", _NAMES)
def test_a_pie_companion_table_paints_a_native(name: str, tmp_path: Path) -> None:
    chart = "    type: pie\n    theta: secs\n    color: a\n"
    result = _render(
        _board(
            chart,
            query_formats=f"    formats:\n      secs: {name}",
            rows=_TABLE_ROWS,
        ),
        tmp_path,
    )
    assert _codes(result) == []
    assert _NATIVE_PAINTED[name][0] in _text(result)


_PAINTED = {"id": "38", "latitude": "37.7749°", "longitude": "37.7749°"}
_PAINTED["rating"] = "37.8"


@pytest.mark.parametrize("name", list(_PAINTED))
@pytest.mark.parametrize("where", ["column", "default"])
def test_a_table_paints_the_spec_kind_names(
    name: str, where: str, tmp_path: Path
) -> None:
    chart, style = _TABLE, ""
    if where == "column":
        chart += (
            f"    style:\n      columns:\n        secs:\n          format: {name}\n"
        )
    else:
        style = f"style:\n  formats:\n    number: {name}"
    result = _render(_board(chart, style=style, rows='[["a", 37.7749]]'), tmp_path)
    assert _codes(result) == []
    painted = _text(result)
    assert _PAINTED[name] in painted
    assert name not in painted


@pytest.mark.parametrize("name", list(_PAINTED))
def test_a_kpi_paints_a_spec_kind_name_as_the_number_default(
    name: str, tmp_path: Path
) -> None:
    kpi = "    type: kpi\n    value: secs\n"
    result = _render(
        _board(
            kpi,
            style=f"style:\n  formats:\n    number: {name}",
            rows='[["a", 37.7749]]',
        ),
        tmp_path,
    )
    assert _codes(result) == []
    assert _PAINTED[name] in _text(result)


def test_an_authored_prefix_replaces_the_unit_in_the_table_kpi_and_column(
    tmp_path: Path,
) -> None:
    default = 'style:\n  formats:\n    number:\n      spec: latitude\n      prefix: "~"'
    column = (
        _TABLE
        + '    style:\n      columns:\n        secs:\n          format:\n            spec: latitude\n            prefix: "~"\n'
    )
    rows = '[["a", 37.7749]]'
    kpi = "    type: kpi\n    value: secs\n"
    for chart, style in [(_TABLE, default), (kpi, default), (column, "")]:
        painted = _text(_render(_board(chart, style=style, rows=rows), tmp_path))
        assert "~37.7749" in painted
        assert "°" not in painted
        assert "latitude" not in painted


@pytest.mark.parametrize(
    "chart",
    [
        "    type: table\n    style:\n      columns:\n        d:\n          format: date_month\n",
        "    type: kpi\n    value: d\n    style:\n      value:\n        format: date_month\n",
    ],
)
def test_a_redefined_grain_name_paints_in_a_table_and_a_kpi(
    chart: str, tmp_path: Path
) -> None:
    board = _board(
        chart,
        style='style:\n  formats:\n    date_month: "%b %Y"',
        rows='[["2024-01-15", 5]]',
    ).replace("[a, secs]", "[d, v]")
    result = _render(board, tmp_path)
    assert _codes(result) == []
    assert ">Jan 2024<" in _svg(result)
