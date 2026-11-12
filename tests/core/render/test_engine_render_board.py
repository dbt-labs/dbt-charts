"""`_engine.render_board` draws a compiled board over recorded query results and reports
each chart's cell: the one entry production and the equivalence chart-side both run."""

import json
from pathlib import Path

import pytest

from dbt_charts import _engine
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.engine import engine_compile
from dbt_charts.core.fonts import get_fonts_dir

BOARD = """\
source: wh
queries:
  q:
    sql: select 1 as x, 2 as y
charts:
  dots:
    type: scatter
    query: q
    x: x
    y: y
rows:
  - dots
"""

ROWS = {
    "rows": [{"x": 1, "y": 2}, {"x": 2, "y": 3}, {"x": 3, "y": 5}],
    "schema": {
        column: {"kind": "integer", "element": None, "fields": None}
        for column in ("x", "y")
    },
}


def compiled(tmp_path: Path) -> _engine.CompiledBoard:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "a.yml").write_text(BOARD, encoding="utf-8")
    return engine_compile(FilesystemProject(tmp_path).path("a.yml"))


def render(
    board: _engine.CompiledBoard,
    datasets: dict[str, object],
    chart_errors: dict[str, object] | None = None,
) -> _engine.RenderedBoard:
    return _engine.render_board(
        board,
        json.dumps(datasets),
        json.dumps(chart_errors or {}),
        "{}",
        [],
        _engine.load_shaped_fonts(str(get_fonts_dir())),
        "2026-11-06T00:00:00Z",
    )


def test_a_scatter_board_renders_with_its_chart_cell(tmp_path: Path) -> None:
    rendered = render(compiled(tmp_path), {"a.yml#q": ROWS})
    assert "<svg" in rendered.svg
    assert rendered.boxes["dots"]["height"] > 0
    assert rendered.height >= rendered.boxes["dots"]["height"]
    assert rendered.charts["dots"].startswith("<svg")
    assert rendered.charts["dots"] in rendered.svg


def test_a_query_with_no_results_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(_engine.RenderDiagnosticError) as raised:
        render(compiled(tmp_path), {})
    assert raised.value.code == "ERR-ENGINE-NEEDS-DATA"


def execute_error(**fields: str) -> dict[str, object]:
    return {
        "code": "ERR-BINDER-UNKNOWN-RELATION",
        "fields": {"detail": "main.orders", **fields},
        "query_name": "q",
    }


def test_an_execute_error_field_the_code_does_not_register_is_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="does not register"):
        render(compiled(tmp_path), {}, {"dots": execute_error(extra="x")})


TABLE_BOARD = """\
source: wh
queries:
  q:
    sql: select 1 as a
charts:
  t:
    type: table
    query: q
    columns:
      - a
rows:
  - t
"""


def compiled_table(tmp_path: Path) -> _engine.CompiledBoard:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "a.yml").write_text(TABLE_BOARD, encoding="utf-8")
    return engine_compile(FilesystemProject(tmp_path).path("a.yml"))


def test_a_chart_error_with_no_query_is_that_charts_error_card(tmp_path: Path) -> None:
    error: dict[str, object] = {
        "code": "ERR-BINDER-UNKNOWN-RELATION",
        "fields": {"detail": "main.orders"},
    }
    rendered = render(compiled_table(tmp_path), {}, {"t": error})
    assert [d["code"] for d in rendered.diagnostics] == ["ERR-BINDER-UNKNOWN-RELATION"]
    assert "query_name" not in rendered.diagnostics[0]["params"]
    assert "t" in rendered.boxes
