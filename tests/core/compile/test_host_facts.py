"""`CompiledBoard.host_facts_json` reaches Python through one TypedDict reader."""

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.engine import engine_compile
from dbt_charts.core.compile.host_facts import (
    HOST_FACTS_VERSION,
    HostFacts,
    parse_host_facts,
)
from dbt_charts.core.compile.validate.links import iter_board_links
from dbt_charts.core.link_targets import check_link_targets

BOARD = """\
queries:
  q:
    source: wh
    sql: select a, b from {{ ref('orders') }}
    ignore:
      - WARN-FANOUT-RISK
charts:
  bars:
    type: bar
    query: q
    x: a
    y: b
    link: /boards/x
    warnings_ignore:
      - WARN-DOUBLE-HEADER
  bare:
    type: pie
    query: q
rows:
  - bars
  - bare
"""


def facts(tmp_path: Path) -> HostFacts:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "a.yml").write_text(BOARD, encoding="utf-8")
    board = engine_compile(FilesystemProject(tmp_path).path("a.yml"))
    return parse_host_facts(board.host_facts_json())


def test_the_facts_carry_each_query_chart_link_and_suppression(
    tmp_path: Path,
) -> None:
    got = facts(tmp_path)
    assert got["version"] == HOST_FACTS_VERSION
    assert got["queries"]["q"] == {
        "label": "q",
        "authored": True,
        "type": "sql",
        "source": "wh",
        "sql": "select a, b from {{ ref('orders') }}",
        "setup_sql": None,
        "dialect": None,
    }
    assert got["column_refs"] == {"bars": [["q", "x", "a"], ["q", "y", "b"]]}
    assert got["unbound_charts"] == [
        {"chart": "bare", "type": "pie", "query": "q", "side_columns": {}}
    ]
    assert got["links"] == [["charts.bars.link", "/boards/x", False]]
    assert got["warnings_ignore"] == {"bars": ["WARN-DOUBLE-HEADER"]}


def test_a_reader_refuses_a_version_it_does_not_know() -> None:
    with pytest.raises(ValueError, match="version 2"):
        parse_host_facts('{"version": 2}')


NESTED = """\
source: wh
queries:
  q:
    sql: select 1 as a
charts:
  c:
    type: bar
    query: q
    x: a
rows:
  - style:
      footer:
        link: /nowhere
    rows:
      - c
  - style:
      footer:
        link: /user-360
    rows:
      - c
"""


def test_a_nested_boards_style_links_match_pythons_and_warn_the_same(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n",
        encoding="utf-8",
    )
    (tmp_path / "charts").mkdir(exist_ok=True)
    (tmp_path / "charts" / "user-360.yml").write_text("rows: []\n", encoding="utf-8")
    (tmp_path / "charts" / "a.yml").write_text(NESTED, encoding="utf-8")
    project = FilesystemProject(tmp_path)
    boards = project.path("charts/a.yml")
    from_engine = parse_host_facts(engine_compile(boards).host_facts_json())["links"]
    result = compile_file(boards.read_board())
    assert result.board is not None
    from_python = list(iter_board_links(result.board))
    assert [tuple(link) for link in from_engine] == from_python
    got = [
        d.model_dump() for d in check_link_targets(from_engine, project, "charts/a.yml")
    ]
    assert got == [
        d.model_dump() for d in check_link_targets(from_python, project, "charts/a.yml")
    ]
    assert [d["field"] for d in got] == ["style.footer.link"]
