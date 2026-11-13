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
    presets: str = "{}",
) -> _engine.RenderedBoard:
    return _engine.render_board(
        board,
        json.dumps(datasets),
        json.dumps(chart_errors or {}),
        "{}",
        presets,
        [],
        _engine.load_shaped_fonts(str(get_fonts_dir())),
        "2026-11-06T00:00:00Z",
        None,
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


NESTED_KPI_BOARD = """\
queries:
  q:
    type: values
    columns: [a]
    values:
      - [1]
rows:
  - grid:
      columns: 24
      items:
        - item: k
          width: 24
    charts:
      k:
        type: kpi
        query: queries.q
        value: a
"""


def test_a_chart_authored_in_a_nested_board_wrapper_carries_its_authored_path(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.yml").write_text(NESTED_KPI_BOARD, encoding="utf-8")
    board = engine_compile(FilesystemProject(tmp_path).path("a.yml"))
    rendered = render(
        board,
        {
            "a.yml#q": {
                "rows": [{"a": 1}],
                "schema": {"a": {"kind": "integer", "element": None, "fields": None}},
            }
        },
    )
    assert 'data-authored-path="rows.0.charts.k"' in rendered.svg
    assert 'data-authored-path="charts.k"' not in rendered.svg


ROOT_KPI_IN_NESTED_BOARD = """\
queries:
  q:
    type: values
    columns: [a]
    values:
      - [1]
charts:
  k:
    type: kpi
    query: queries.q
    value: a
rows:
  - rows:
      - k
"""


def test_a_chart_defined_at_the_root_keeps_its_path_in_a_nested_board(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.yml").write_text(ROOT_KPI_IN_NESTED_BOARD, encoding="utf-8")
    board = engine_compile(FilesystemProject(tmp_path).path("a.yml"))
    rendered = render(
        board,
        {
            "a.yml#q": {
                "rows": [{"a": 1}],
                "schema": {"a": {"kind": "integer", "element": None, "fields": None}},
            }
        },
    )
    assert 'data-authored-path="charts.k"' in rendered.svg
    assert 'data-authored-path="rows.0.charts.k"' not in rendered.svg


KPI_BOARD = """\
source: wh
queries:
  q:
    sql: select 4820 as revenue
charts:
  revenue:
    type: kpi
    query: q
    value: revenue
    label: Revenue
rows:
  - revenue
"""

KPI_ROWS = {
    "rows": [{"revenue": 4820}],
    "schema": {"revenue": {"kind": "integer", "element": None, "fields": None}},
}


def compiled_kpi(tmp_path: Path) -> _engine.CompiledBoard:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "a.yml").write_text(KPI_BOARD, encoding="utf-8")
    return engine_compile(FilesystemProject(tmp_path).path("a.yml"))


def in_cell(
    board: _engine.CompiledBoard,
    placement: str,
    height: float | None,
) -> _engine.RenderedCell:
    return _engine.render_chart_in_cell(
        board,
        json.dumps({"a.yml#q": KPI_ROWS}),
        "{}",
        "{}",
        "{}",
        [],
        _engine.load_shaped_fonts(str(get_fonts_dir())),
        "2026-11-06T00:00:00Z",
        None,
        placement,
        268.0,
        height,
        (16.0, 16.0, 16.0, 16.0),
    )


def test_a_kpi_draws_in_the_cell_the_host_sizes(tmp_path: Path) -> None:
    drawn = in_cell(compiled_kpi(tmp_path), "revenue", 48.0)
    assert drawn.svg.startswith("<svg")
    assert "4.8" in drawn.svg
    assert drawn.height >= 48.0
    assert drawn.diagnostics == []


def test_a_kpi_with_no_height_draws_at_its_own(tmp_path: Path) -> None:
    drawn = in_cell(compiled_kpi(tmp_path), "revenue", None)
    assert drawn.height > 0


def test_a_chart_the_board_does_not_place_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(_engine.RenderDiagnosticError):
        in_cell(compiled_kpi(tmp_path), "elsewhere", 48.0)


def test_a_preset_for_a_variable_the_board_does_not_declare_is_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(_engine.RenderDiagnosticError) as raised:
        render(
            compiled(tmp_path),
            {"a.yml#q": ROWS},
            presets='{"span": "last_7_days"}',
        )
    assert raised.value.code == "ERR-INTERNAL"
    assert "span is not a declared variable" in str(raised.value)
