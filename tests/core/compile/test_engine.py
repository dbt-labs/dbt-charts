"""`engine_compile` reads a project and calls the Rust compile with the live analyzer."""

import json
from pathlib import Path

import pytest

from dbt_charts import _engine
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.engine import engine_compile, engine_index

from ..._paths import DBT_CHARTS_DIR

BOARD = """\
title: Sales
extends: base
queries:
  q:
    source: wh
    sql: select a from t
charts:
  t:
    type: table
    query: q
    columns:
      - a
rows:
  - t
"""


def project(tmp_path: Path, files: dict[str, str]) -> FilesystemProject:
    files = {
        "dbt_charts.yml": "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n",
        **files,
    }
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return FilesystemProject(tmp_path)


def test_a_board_compiles_through_the_project_and_its_sql_is_analyzed(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path, {"charts/a.yml": BOARD, "base.yml": "title: Base"})
    board = engine_compile(proj.path("charts/a.yml"))
    assert json.loads(board.index_json())["public_ids"] == {"t": "t"}
    assert board.diagnostics() == []


def test_a_refused_board_raises_with_its_codes(tmp_path: Path) -> None:
    proj = project(tmp_path, {"charts/a.yml": "bogus_key: 1\n"})
    with pytest.raises(_engine.CompileError) as raised:
        engine_compile(proj.path("charts/a.yml"))
    assert [d["code"] for d in raised.value.diagnostics] == ["ERR-EXTRA-FIELD"]


RETIRED_KEY_BOARD = """\
titel: Revenue
queries:
  revenue:
    sql: SELECT month, SUM(revenue) AS revenue FROM orders GROUP BY 1
    source: wh
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: month
    y: revenue
    style:
      axis_y:
        grid:
          zero:
            color: red
rows:
  - revenue_trend
"""


def test_a_refusal_names_the_successor_of_a_retired_key_and_keeps_the_migration_warning(
    tmp_path: Path,
) -> None:
    from dbt_charts.agent_api.validate import _refusal_diagnostics

    proj = project(tmp_path, {"charts/a.yml": RETIRED_KEY_BOARD})
    with pytest.raises(_engine.CompileError) as raised:
        engine_compile(proj.path("charts/a.yml"))

    errors = _refusal_diagnostics(raised.value, "charts/a.yml")
    zero = next(e for e in errors if e.code == "ERR-RETIRED-FIELD")
    assert (
        "'zero' at charts.revenue_trend.style.axis_y.grid was renamed" in zero.message
    )
    assert "in 0.7.0" in zero.message
    assert (
        zero.fields["successor"] == "charts.revenue_trend.style.axis_y.grid.threshold"
    )
    wire = {d["code"]: d for d in raised.value.diagnostics}
    assert wire["ERR-RETIRED-FIELD"]["range"] is not None
    assert "Did you mean 'title'?" in wire["ERR-EXTRA-FIELD"]["hint"]
    assert [w["code"] for w in raised.value.warnings] == ["WARN-SCHEMA-MIGRATED"]


def test_the_compile_reads_the_projects_own_html_ceiling(tmp_path: Path) -> None:
    proj = project(
        tmp_path,
        {
            "dbt_charts.yml": "html_policy_ceiling: nope\n",
            "charts/a.yml": "rows:\n  - text: hi\n",
        },
    )
    with pytest.raises(_engine.CompileError, match="html_policy_ceiling"):
        engine_compile(proj.path("charts/a.yml"))


def test_a_dbt_projects_implicit_source_serves_a_query_naming_none(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: shop\nprofile: shop\n")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "a.yml").write_text(
        "queries:\n  q:\n    sql: select 1 as a\n"
        "charts:\n  c:\n    type: table\n    query: q\n    columns:\n      - a\n"
        "rows:\n  - c\n"
    )
    board = engine_compile(FilesystemProject(tmp_path).path("charts/a.yml"))
    assert json.loads(board.index_json())["public_ids"] == {"c": "c"}


def test_a_fragment_that_raises_on_read_reads_as_absent(tmp_path: Path) -> None:
    from dbt_charts.core.diagnostics.execution import ExecutionError

    class Oversized(FilesystemProject):
        def read_text(self, relpath: str) -> str:
            if relpath == "charts/_base.yml":
                raise ExecutionError("too large")
            return super().read_text(relpath)

    project(tmp_path, {"charts/a.yml": "extends: ./_base.yml\nrows:\n  - text: hi\n"})
    (tmp_path / "charts" / "_base.yml").write_text("title: Base\n")
    # The board names a file that reads as absent: refused, not a panic.
    assert engine_index(Oversized(tmp_path).path("charts/a.yml")) is None


def test_a_board_whose_sql_will_not_parse_is_indexed_not_refused(
    tmp_path: Path,
) -> None:
    proj = project(
        tmp_path,
        {
            "charts/a.yml": "source: wh\nqueries:\n  q:\n    sql: drop table t\n"
            "charts:\n  c:\n    type: table\n    query: q\nrows:\n  - c\n"
        },
    )
    path = proj.path("charts/a.yml")
    with pytest.raises(_engine.CompileError):
        engine_compile(path)
    index = engine_index(path)
    assert index is not None
    assert [q["name"] for q in index["queries"]] == ["q"]


PLAYGROUND = DBT_CHARTS_DIR / "examples" / "playground"


@pytest.mark.parametrize(
    "board", ["charts/reference/styling.yml", "charts/reference/color-palettes.yml"]
)
def test_the_engine_compiles_the_playground_boards_with_px_gaps(board: str) -> None:
    engine_compile(FilesystemProject(PLAYGROUND).path(board))


_CARTESIAN_JOIN = """\
queries:
  revenue:
    source: wh
    sql: |
      {comment}
      SELECT SUM(o.amount), SUM(oi.qty)
      FROM orders o, order_items oi
      GROUP BY o.id
charts:
  revenue_trend:
    query: revenue
    type: bar
    x: o.id
    y: revenue
rows:
  - revenue_trend
"""


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("-- a note", ["WARN-MISSING-JOIN-PREDICATE"]),
        ("-- dct:ignore WARN-MISSING-JOIN-PREDICATE WARN-REAGGREGATION", []),
        ("-- dct:ignore", []),
        ("-- dct:ignore WARN-REAGGREGATION", ["WARN-MISSING-JOIN-PREDICATE"]),
    ],
)
def test_a_dct_ignore_comment_suppresses_the_engine_query_lints(
    tmp_path: Path, comment: str, expected: list[str]
) -> None:
    proj = project(tmp_path, {"a.yml": _CARTESIAN_JOIN.format(comment=comment)})
    codes = [d["code"] for d in engine_compile(proj.path("a.yml")).diagnostics()]
    assert [c for c in codes if c.startswith("WARN-MISSING")] == expected
